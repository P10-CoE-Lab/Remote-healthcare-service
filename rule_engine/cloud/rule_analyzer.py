"""
rule_engine/cloud/rule_analyzer.py
------------------------------------
Threshold-based windowed rule evaluation.

Implements the Analyzer interface using cloud_rules.yaml configuration:
- Single-condition rules: one sensor must breach threshold for the window
- Multi-condition rules: all conditions must independently be sustained
- Weight-based risk score: sum of active rule weights, capped at 100

This is the default Analyzer — a future ML-based implementation can be
swapped in by passing a different Analyzer to CloudEngine.
"""

from __future__ import annotations

import time
from collections import deque

from rule_engine.cloud.analyzer import Analyzer, AnalyzerAlert
from rule_engine.cloud.config import CloudCondition, CloudConfig, CloudRule
from rule_engine.shared.logger import get_logger

logger = get_logger(__name__)

_OPERATORS = {
    ">":  lambda v, t: v > t,
    "<":  lambda v, t: v < t,
    ">=": lambda v, t: v >= t,
    "<=": lambda v, t: v <= t,
    "==": lambda v, t: v == t,
}


class RuleAnalyzer(Analyzer):
    """Windowed threshold rule evaluator backed by cloud_rules.yaml."""

    def __init__(self, config: CloudConfig) -> None:
        self._config = config
        # (rule_id, device_id) → monotonic time of last alert
        self._last_alert: dict[tuple[str, str], float] = {}

    def analyze(
        self,
        device_id:      str,
        persona_id:     str,
        sensor_buffers: dict[str, deque],
        compression:    float,
        now:            float,
    ) -> list[AnalyzerAlert]:
        """Evaluate all rules and return alerts for any that fire."""
        now_mono     = time.monotonic()
        active_rules: list[CloudRule] = []
        total_weight = 0.0

        for rule in self._config.rules:
            if self._is_cooling_down(rule, device_id, now_mono):
                continue

            if rule.multi_conditions:
                fired = self._check_multi(rule, sensor_buffers, now, compression)
            elif rule.condition:
                fired = self._check_single(rule, rule.condition, sensor_buffers, now, compression)
            else:
                continue

            if fired:
                active_rules.append(rule)
                total_weight += rule.weight

        if not active_rules:
            return []

        risk_score = min(total_weight, 100.0)
        risk_level = self._score_to_level(risk_score)

        alerts: list[AnalyzerAlert] = []
        for rule in active_rules:
            if rule.multi_conditions:
                trigger_sensor = rule.multi_conditions[0].sensor
                trigger_value  = self._last_value(sensor_buffers, trigger_sensor)
                threshold      = rule.multi_conditions[0].threshold
                conditions_met = [
                    f"{c.sensor} {c.operator} {c.threshold} sustained {int(rule.window_seconds)}s"
                    for c in rule.multi_conditions
                ]
            else:
                cond           = rule.condition
                trigger_sensor = cond.sensor
                trigger_value  = self._last_value(sensor_buffers, trigger_sensor)
                threshold      = cond.threshold
                conditions_met = [
                    f"{cond.sensor} {cond.operator} {cond.threshold} sustained {int(rule.window_seconds)}s"
                ]

            alerts.append(AnalyzerAlert(
                rule_id=        rule.id,
                description=    rule.description,
                severity=       rule.severity,
                sensor_name=    trigger_sensor,
                sensor_value=   trigger_value,
                threshold=      threshold,
                risk_score=     risk_score,
                risk_level=     risk_level,
                conditions_met= conditions_met,
            ))
            self._last_alert[(rule.id, device_id)] = now_mono

        return alerts

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_single(
        self,
        rule: CloudRule,
        cond: CloudCondition,
        sensor_buffers: dict,
        now: float,
        compression: float = 1.0,
    ) -> bool:
        buf = sensor_buffers.get(cond.sensor)
        if not buf or len(buf) < 2:
            return False

        effective_window = rule.window_seconds / max(compression, 1.0)

        # Require the buffer to have been collecting for at least effective_window
        # seconds before we evaluate. This is the "have we seen enough history?"
        # guard. We cannot use the span of window_readings for this because
        # window_readings is already filtered to the last effective_window seconds,
        # so its span is always less than effective_window by construction.
        buffer_span = buf[-1][0] - buf[0][0]
        if buffer_span < effective_window:
            return False

        window_readings = [(ts, v) for ts, v in buf if ts >= now - effective_window]
        if len(window_readings) < 2:
            return False

        op = _OPERATORS.get(cond.operator)
        if op is None:
            return False
        breached = sum(1 for _, v in window_readings if op(v, cond.threshold))
        return breached / len(window_readings) >= rule.fraction_required

    def _check_multi(
        self,
        rule: CloudRule,
        sensor_buffers: dict,
        now: float,
        compression: float = 1.0,
    ) -> bool:
        return all(
            self._check_single(rule, cond, sensor_buffers, now, compression)
            for cond in rule.multi_conditions
        )

    def _last_value(self, sensor_buffers: dict, sensor_name: str) -> float:
        buf = sensor_buffers.get(sensor_name)
        return buf[-1][1] if buf else 0.0

    def _is_cooling_down(self, rule: CloudRule, device_id: str, now_mono: float) -> bool:
        last = self._last_alert.get((rule.id, device_id))
        if last is None:
            return False
        return (now_mono - last) < rule.cooldown_seconds

    def _score_to_level(self, score: float) -> str:
        levels = self._config.risk_levels
        if score >= levels.get("critical", 80):
            return "critical"
        if score >= levels.get("high", 60):
            return "high"
        if score >= levels.get("medium", 30):
            return "medium"
        if score >= levels.get("low", 10):
            return "low"
        return "none"
