"""
simulator/rules/risk_scoring.py
--------------------------------
Real-time worker safety risk scoring engine.

Implements rules R1-R10 and computes aggregate risk score/level.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Dict, List

from simulator.sensors.base import SensorReading

from .risk_loader import RiskRule, RiskScoringConfig


@dataclass
class RiskSnapshot:
    """Point-in-time risk score output."""
    timestamp: float
    risk_score: int
    risk_level: str
    active_rule_ids: List[str]


class RiskScoringEngine:
    """Evaluates configured risk rules and returns aggregate risk score."""

    def __init__(self, config: RiskScoringConfig) -> None:
        self._config = config
        self._rules_by_id: Dict[str, RiskRule] = {
            r.id: r for r in config.rules if r.enabled
        }
        self._latest_sensor_values: Dict[str, float] = {}
        self._context: Dict[str, Any] = {}

        self._condition_start_time: Dict[str, float] = {}
        self._active_rule_ids: set[str] = set()
        self._recent_safety_events: deque[tuple[float, str]] = deque(maxlen=500)
        self._recent_snapshots: deque[RiskSnapshot] = deque(maxlen=1000)

        self._fall_detected_until: float = 0.0
        self._last_zone_state: tuple[bool, str | None] = (False, None)

    def process_sensor_reading(self, reading: SensorReading) -> None:
        """Ingest latest sensor values for risk evaluation."""
        self._latest_sensor_values[reading.sensor_name] = float(reading.value)

        if reading.sensor_name == "accel_magnitude":
            fall_threshold = self._rule_param("R5", "accel_fall_threshold", 3.0)
            if float(reading.value) >= float(fall_threshold):
                self._fall_detected_until = max(self._fall_detected_until, time.time() + 2.0)

    def process_context_update(self, context: Dict[str, Any]) -> None:
        """Ingest non-sensor context: zone/machine/PPE/shift/location."""
        self._context.update(context)

        if context.get("fall_detected") is True:
            self._fall_detected_until = max(self._fall_detected_until, time.time() + 2.0)

        safety_event = context.get("safety_event")
        if safety_event:
            self.process_safety_event(str(safety_event))

    def process_safety_event(self, event_type: str) -> None:
        """Track safety events for R10 rolling count logic."""
        self._recent_safety_events.append((time.time(), event_type))

    def evaluate(self, current_time: float) -> RiskSnapshot:
        """Evaluate all risk rules and return current risk snapshot."""
        new_active: set[str] = set()

        for rule in self._rules_by_id.values():
            raw_match = self._evaluate_rule_condition(rule, current_time)

            if rule.evaluation_window_seconds <= 0:
                is_active = raw_match
            else:
                is_active = self._evaluate_windowed_condition(rule.id, raw_match, current_time, rule.evaluation_window_seconds)

            if is_active:
                new_active.add(rule.id)

        newly_activated = new_active - self._active_rule_ids
        for rule_id in newly_activated:
            if rule_id != "R10":
                self.process_safety_event(f"risk_rule:{rule_id}")

        self._active_rule_ids = new_active

        score = sum(self._rules_by_id[rid].weight for rid in sorted(new_active))
        level = self._score_to_level(score)
        snapshot = RiskSnapshot(
            timestamp=current_time,
            risk_score=score,
            risk_level=level,
            active_rule_ids=sorted(new_active),
        )
        self._recent_snapshots.append(snapshot)
        return snapshot

    def get_status(self) -> Dict[str, Any]:
        """Return latest risk scoring state."""
        latest = self._recent_snapshots[-1] if self._recent_snapshots else RiskSnapshot(
            timestamp=time.time(),
            risk_score=0,
            risk_level="none",
            active_rule_ids=[],
        )
        return {
            "enabled": True,
            "risk_score": latest.risk_score,
            "risk_level": latest.risk_level,
            "active_rule_ids": latest.active_rule_ids,
            "configured_rules": sorted(self._rules_by_id.keys()),
            "last_evaluated_at": latest.timestamp,
        }

    def get_recent_snapshots(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent risk snapshots for debugging/dashboard."""
        recent = list(self._recent_snapshots)[-limit:]
        return [
            {
                "timestamp": s.timestamp,
                "risk_score": s.risk_score,
                "risk_level": s.risk_level,
                "active_rule_ids": s.active_rule_ids,
            }
            for s in recent
        ]

    def _evaluate_windowed_condition(self, rule_id: str, raw_match: bool, now: float, window_seconds: float) -> bool:
        if raw_match:
            if rule_id not in self._condition_start_time:
                self._condition_start_time[rule_id] = now
            return (now - self._condition_start_time[rule_id]) >= window_seconds

        self._condition_start_time.pop(rule_id, None)
        return False

    def _evaluate_rule_condition(self, rule: RiskRule, now: float) -> bool:
        ctype = rule.condition_type

        if ctype == "hazard_zone_entry":
            zone_types = set(rule.parameters.get("zone_types", ["hazard", "restricted"]))
            in_zone = bool(self._context.get("in_zone", False))
            zone_type = self._context.get("zone_type")
            prev_in_zone, prev_zone = self._last_zone_state
            entered = in_zone and ((not prev_in_zone) or (prev_zone != zone_type))
            self._last_zone_state = (in_zone, zone_type)
            return entered and zone_type in zone_types

        if ctype == "unsafe_machine_proximity":
            distance = self._context.get("distance_to_machine")
            danger_radius = self._context.get("danger_radius", rule.parameters.get("danger_radius", 2.0))
            machine_state = str(self._context.get("machine_state", "")).lower()
            return self._is_number(distance) and float(distance) < float(danger_radius) and machine_state == "running"

        if ctype == "stationary_near_running_machine":
            motion_threshold = float(rule.parameters.get("motion_threshold", 0.08))
            distance = self._context.get("distance_to_machine")
            danger_radius = self._context.get("danger_radius", rule.parameters.get("danger_radius", 2.0))
            machine_state = str(self._context.get("machine_state", "")).lower()
            motion_level = self._motion_level()
            return (
                motion_level < motion_threshold
                and self._is_number(distance)
                and float(distance) < float(danger_radius)
                and machine_state == "running"
            )

        if ctype == "ppe_violation_hazard_zone":
            zone_type = str(self._context.get("zone_type", "")).lower()
            in_zone = bool(self._context.get("in_zone", True))
            ppe_detected = self._context.get("ppe_detected", True)
            return zone_type == "hazard" and in_zone and (ppe_detected is False)

        if ctype == "fall_detected":
            fall_flag = bool(self._context.get("fall_detected", False))
            return fall_flag or now <= self._fall_detected_until

        if ctype == "heart_rate_spike":
            hr = self._latest_sensor_values.get("heart_rate")
            threshold = float(rule.parameters.get("heart_rate_threshold", 140))
            return self._is_number(hr) and float(hr) > threshold

        if ctype == "late_shift_fatigue":
            hr = self._latest_sensor_values.get("heart_rate")
            hr_threshold = float(rule.parameters.get("fatigue_hr_threshold", 95))
            hours_worked = self._context.get("hours_worked", 0)
            hours_threshold = float(rule.parameters.get("hours_worked_threshold", 6))
            return self._is_number(hr) and self._is_number(hours_worked) and float(hr) > hr_threshold and float(hours_worked) > hours_threshold

        if ctype == "hazard_zone_dwell_time":
            zone_type = str(self._context.get("zone_type", "")).lower()
            in_zone = bool(self._context.get("in_zone", False))
            time_in_zone = self._context.get("time_in_zone_seconds", 0)
            dwell_threshold = float(rule.parameters.get("time_in_zone_threshold_seconds", 120))
            return zone_type == "hazard" and in_zone and self._is_number(time_in_zone) and float(time_in_zone) > dwell_threshold

        if ctype == "machine_overheating_near_worker":
            machine_temp = self._context.get("machine_temperature")
            temp_threshold = float(rule.parameters.get("machine_temperature_threshold", 85))
            distance = self._context.get("distance_to_machine")
            danger_radius = self._context.get("danger_radius", rule.parameters.get("danger_radius", 2.0))
            return (
                self._is_number(machine_temp)
                and float(machine_temp) > temp_threshold
                and self._is_number(distance)
                and float(distance) < float(danger_radius)
            )

        if ctype == "multiple_violations_short_period":
            window_seconds = float(rule.parameters.get("window_seconds", rule.evaluation_window_seconds or 180))
            threshold = int(rule.parameters.get("events_threshold", 3))
            cutoff = now - window_seconds
            while self._recent_safety_events and self._recent_safety_events[0][0] < cutoff:
                self._recent_safety_events.popleft()
            return len(self._recent_safety_events) >= threshold

        return False

    def _motion_level(self) -> float:
        explicit = self._context.get("motion_level")
        if self._is_number(explicit):
            return float(explicit)

        accel = self._latest_sensor_values.get("accel_magnitude")
        if self._is_number(accel):
            return abs(float(accel) - 1.0)

        return 1.0

    def _rule_param(self, rule_id: str, key: str, default: Any) -> Any:
        rule = self._rules_by_id.get(rule_id)
        if not rule:
            return default
        return rule.parameters.get(key, default)

    def _score_to_level(self, score: int) -> str:
        t = self._config.level_thresholds
        if score >= t.critical:
            return "critical"
        if score >= t.high:
            return "high"
        if score >= t.medium:
            return "medium"
        if score >= t.low:
            return "low"
        return "none"

    @staticmethod
    def _is_number(value: Any) -> bool:
        try:
            float(value)
            return True
        except (TypeError, ValueError):
            return False
