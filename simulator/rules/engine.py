"""
simulator/rules/engine.py
-------------------------
Core rules engine for the Vitals Simulator.

Monitors sensor readings and triggers rules based on configured conditions.
Separated from the main simulation engine to provide clear separation of concerns.

Architecture:
    - Periodic evaluation of all enabled rules
    - Condition state tracking with duration requirements
    - Alert cooldown and deduplication
    - Integration with MQTT publisher and scenario events

Usage:
    from simulator.rules import RulesEngine, load_rules_config
    
    rules_config = load_rules_config("config/rules_config.yaml")
    engine = RulesEngine(rules_config, mqtt_publisher)
    await engine.start()
    
    # Feed sensor readings
    engine.process_sensor_reading(reading, persona_id, poc_type, device_id)
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .loader import (
    Rule, RuleCondition, RuleAction, RulesConfig, TimeConstraint
)
from .risk_loader import load_risk_scoring_config
from .risk_scoring import RiskScoringEngine
from .timing_policy import load_timing_policy
from simulator.sensors.base import SensorReading
from simulator.utils.logger import get_logger

logger = get_logger(__name__)


# Fallback risk weights for threshold-rule violations when real-time risk
# windows have not matured yet.
_RULE_RISK_FALLBACK_WEIGHTS: dict[str, int] = {
    "heart_rate_elevated": 60,          # aligns with H2 tachycardia
    "temperature_heat_stress": 60,
    "posture_unsafe": 30,
    "fall_detection": 80,               # aligns with R5 fall risk
    "spo2_low": 80,                     # aligns with H1 low SpO2
    "heart_rate_variability_low": 40,   # aligns with H4 low HRV
    "fatigue_combination": 25,          # aligns with R7 fatigue risk
}


# ---------------------------------------------------------------------------
# Rule evaluation state tracking
# ---------------------------------------------------------------------------

@dataclass
class SensorDataPoint:
    """A single sensor reading with timestamp."""
    value: float
    timestamp: float
    condition: str
    quality: str


@dataclass
class ConditionState:
    """Tracks the state of a rule condition over time."""
    condition: RuleCondition
    first_violation_time: Optional[float] = None
    last_evaluation_time: float = 0.0
    is_currently_violated: bool = False
    violation_count: int = 0


@dataclass
class RuleState:
    """Tracks the overall state of a rule."""
    rule: Rule
    condition_states: Dict[str, ConditionState] = field(default_factory=dict)
    last_trigger_time: Optional[float] = None
    trigger_count: int = 0
    is_currently_triggered: bool = False


@dataclass
class RuleViolation:
    """Record of a rule violation for history/logging."""
    rule_id: str
    rule_name: str
    timestamp: float
    persona_id: str
    poc_type: str
    sensor_readings: Dict[str, Any]
    triggered_conditions: List[str]
    severity: str
    category: str
    risk_score: int | None = None
    risk_level: str | None = None


# ---------------------------------------------------------------------------
# Rules engine
# ---------------------------------------------------------------------------

class RulesEngine:
    """
    Core rules engine for monitoring sensor data and triggering alerts.
    
    Evaluates rules periodically based on incoming sensor readings and
    maintains state for duration-based conditions.
    
    This engine operates independently of the main simulation engine,
    providing clear separation between data generation and monitoring logic.
    """
    
    def __init__(
        self,
        config: RulesConfig,
        mqtt_publisher: Any = None,
        scenario_engine: Any = None,
        risk_config_path: Path | str | None = None,
        timing_policy_path: Path | str | None = None,
    ) -> None:
        self._config = config
        self._mqtt_publisher = mqtt_publisher
        self._scenario_engine = scenario_engine
        self._risk_engine: RiskScoringEngine | None = None

        # Unified timing policy for threshold + risk rules
        self._timing_policy_path = timing_policy_path or Path("config/rule_timing_policy.yaml")
        self._timing_policy = load_timing_policy(self._timing_policy_path)
        self._apply_timing_policy_to_threshold_rules()
        
        # Sensor data storage (sensor_name -> deque of SensorDataPoint)
        self._sensor_data: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=1000)  # Keep last 1000 readings per sensor
        )
        
        # Rule state tracking
        self._rule_states: Dict[str, RuleState] = {}
        self._initialize_rule_states()
        
        # Violation history
        self._violation_history: deque[RuleViolation] = deque(
            maxlen=self._config.engine.max_violation_history
        )
        
        # Runtime state
        self._running = False
        self._evaluation_task: Optional[asyncio.Task] = None
        self._last_evaluation_time = 0.0
        
        # Alert cooldown tracking (rule_id -> last_alert_time)
        self._alert_cooldowns: Dict[str, float] = {}
        
        # Current persona context (needed for rule evaluation)
        self._current_persona_id: Optional[str] = None
        self._current_poc_type: Optional[str] = None
        self._current_device_id: Optional[str] = None

        # Risk scoring engine (R1-R10)
        try:
            risk_cfg = load_risk_scoring_config(
                risk_config_path or Path("config/risk_rules.yaml")
            )
            self._apply_timing_policy_to_risk_rules(risk_cfg)
            self._risk_engine = RiskScoringEngine(risk_cfg)
            logger.info(
                "Risk scoring engine initialized",
                extra={
                    "event": "risk_engine_init",
                    "rules_count": len(risk_cfg.rules),
                }
            )
        except Exception as e:
            logger.warning(
                "Risk scoring engine disabled",
                extra={"event": "risk_engine_disabled", "error": str(e)}
            )

    def _apply_timing_policy_to_threshold_rules(self) -> None:
        """Apply unified timing policy to threshold rule durations."""
        windows = self._timing_policy.rule_windows_seconds
        if not windows:
            return

        for rule in self._config.rules:
            if rule.id not in windows:
                continue

            duration = windows[rule.id]
            for condition in rule.conditions:
                condition.duration_seconds = duration

    def _apply_timing_policy_to_risk_rules(self, risk_cfg: Any) -> None:
        """Apply unified timing policy to risk rule evaluation windows."""
        windows = self._timing_policy.rule_windows_seconds
        if not windows:
            return

        for rule in risk_cfg.rules:
            if rule.id in windows:
                rule.evaluation_window_seconds = windows[rule.id]
        
        logger.info(
            "Rules engine initialized",
            extra={
                "event": "rules_engine_init",
                "rules_count": len(self._config.rules),
                "enabled_rules": len([r for r in self._config.rules if r.enabled]),
                "evaluation_interval": self._config.engine.evaluation_interval_seconds,
            }
        )
    
    def _initialize_rule_states(self) -> None:
        """Initialize state tracking for all rules."""
        for rule in self._config.rules:
            if not rule.enabled:
                continue
                
            condition_states = {}
            for condition in rule.conditions:
                condition_states[condition.sensor] = ConditionState(condition=condition)
            
            self._rule_states[rule.id] = RuleState(
                rule=rule,
                condition_states=condition_states,
            )

    def reset_runtime_state(self) -> None:
        """Clear runtime violation/risk state when loading a new scenario."""
        self._violation_history.clear()
        self._alert_cooldowns.clear()
        self._sensor_data.clear()
        self._last_evaluation_time = 0.0
        self._current_persona_id = None
        self._current_poc_type = None
        self._current_device_id = None

        for rule_state in self._rule_states.values():
            rule_state.last_trigger_time = None
            rule_state.trigger_count = 0
            rule_state.is_currently_triggered = False
            for condition_state in rule_state.condition_states.values():
                condition_state.first_violation_time = None
                condition_state.last_evaluation_time = 0.0
                condition_state.is_currently_violated = False
                condition_state.violation_count = 0

        if self._risk_engine:
            # Recreate risk engine to reset internal windows/snapshots/events.
            risk_cfg = self._risk_engine._config
            self._risk_engine = RiskScoringEngine(risk_cfg)
    
    async def start(self) -> None:
        """Start the rules engine evaluation loop."""
        if self._running:
            return
        
        self._running = True
        self._evaluation_task = asyncio.create_task(self._evaluation_loop())
        
        logger.info(
            "Rules engine started",
            extra={"event": "rules_engine_started"}
        )
    
    async def stop(self) -> None:
        """Stop the rules engine."""
        self._running = False
        
        if self._evaluation_task and not self._evaluation_task.done():
            self._evaluation_task.cancel()
            try:
                await self._evaluation_task
            except asyncio.CancelledError:
                pass
        
        logger.info(
            "Rules engine stopped",
            extra={"event": "rules_engine_stopped"}
        )
    
    def process_sensor_reading(
        self,
        reading: SensorReading,
        persona_id: str,
        poc_type: str,
        device_id: str,
    ) -> None:
        """
        Process a new sensor reading and update internal data store.
        
        This method provides the data ingestion layer interface for the rules engine.
        It can be called from various sources (MQTT, direct simulation, etc.).
        
        Args:
            reading: The sensor reading to process.
            persona_id: ID of the persona generating the reading.
            poc_type: Type of POC (worker_safety, healthcare).
            device_id: Device identifier.
        """
        # Update context
        self._current_persona_id = persona_id
        self._current_poc_type = poc_type
        self._current_device_id = device_id
        
        # Store the sensor reading
        data_point = SensorDataPoint(
            value=reading.value,
            timestamp=time.time(),
            condition=reading.condition,
            quality=reading.quality,
        )
        
        self._sensor_data[reading.sensor_name].append(data_point)

        # Feed risk engine
        if self._risk_engine:
            self._risk_engine.process_sensor_reading(reading)
        
        if self._config.engine.log_evaluations:
            logger.debug(
                "Sensor reading processed",
                extra={
                    "event": "sensor_reading_processed",
                    "sensor": reading.sensor_name,
                    "value": reading.value,
                    "persona_id": persona_id,
                }
            )
    
    async def _evaluation_loop(self) -> None:
        """Main evaluation loop - runs periodically to check all rules."""
        while self._running:
            try:
                await asyncio.sleep(self._config.engine.evaluation_interval_seconds)
                
                if not self._current_persona_id:
                    continue  # No sensor data received yet
                
                current_time = time.time()
                await self._evaluate_all_rules(current_time)

                # Evaluate risk scoring R1-R10
                if self._risk_engine:
                    risk_snapshot = self._risk_engine.evaluate(current_time)
                    if risk_snapshot.risk_level in {"high", "critical"}:
                        logger.warning(
                            "High worker risk detected",
                            extra={
                                "event": "risk_score_high",
                                "risk_score": risk_snapshot.risk_score,
                                "risk_level": risk_snapshot.risk_level,
                                "active_rules": risk_snapshot.active_rule_ids,
                                "persona_id": self._current_persona_id,
                            }
                        )

                self._last_evaluation_time = current_time
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(
                    "Error in rules evaluation loop",
                    extra={"event": "rules_evaluation_error", "error": str(e)}
                )
                await asyncio.sleep(1.0)  # Back off on errors
    
    async def _evaluate_all_rules(self, current_time: float) -> None:
        """Evaluate all enabled rules against current sensor data."""
        for rule_id, rule_state in self._rule_states.items():
            rule = rule_state.rule
            
            # Check time constraints
            if not self._is_rule_active_now(rule):
                continue
            
            # Evaluate rule conditions
            try:
                triggered = await self._evaluate_rule(rule_state, current_time)
                
                if triggered and not rule_state.is_currently_triggered:
                    # Rule just triggered
                    await self._handle_rule_trigger(rule_state, current_time)
                elif not triggered and rule_state.is_currently_triggered:
                    # Rule stopped triggering
                    rule_state.is_currently_triggered = False
                    
            except Exception as e:
                logger.error(
                    "Error evaluating rule",
                    extra={
                        "event": "rule_evaluation_error",
                        "rule_id": rule_id,
                        "error": str(e)
                    }
                )
    
    def _is_rule_active_now(self, rule: Rule) -> bool:
        """Check if rule should be active based on time constraints."""
        if not rule.time_constraints:
            return True
        
        now = datetime.now()
        tc = rule.time_constraints
        
        # Check hour constraints
        if tc.start_hour is not None and tc.end_hour is not None:
            current_hour = now.hour
            
            if tc.start_hour <= tc.end_hour:
                # Same day range (e.g., 9 AM to 5 PM)
                if not (tc.start_hour <= current_hour < tc.end_hour):
                    return False
            else:
                # Overnight range (e.g., 10 PM to 6 AM)
                if not (current_hour >= tc.start_hour or current_hour < tc.end_hour):
                    return False
        
        # Check day of week constraints
        if tc.days_of_week is not None:
            current_day = now.weekday()  # 0=Monday, 6=Sunday
            if current_day not in tc.days_of_week:
                return False
        
        return True
    
    async def _evaluate_rule(self, rule_state: RuleState, current_time: float) -> bool:
        """Evaluate a single rule and return whether it should trigger."""
        rule = rule_state.rule
        condition_results = []
        
        # Evaluate each condition
        for condition in rule.conditions:
            condition_state = rule_state.condition_states[condition.sensor]
            is_violated = self._evaluate_condition(condition, condition_state, current_time)
            
            condition_state.last_evaluation_time = current_time
            condition_state.is_currently_violated = is_violated
            
            if is_violated:
                if condition_state.first_violation_time is None:
                    condition_state.first_violation_time = current_time
                condition_state.violation_count += 1
            else:
                condition_state.first_violation_time = None
                condition_state.violation_count = 0
            
            # Check duration requirement
            duration_met = True
            if condition.duration_seconds > 0 and condition_state.first_violation_time:
                violation_duration = current_time - condition_state.first_violation_time
                duration_met = violation_duration >= condition.duration_seconds
            
            condition_satisfied = is_violated and duration_met
            condition_results.append(condition_satisfied)
        
        # Apply logical operator
        if rule.logical_operator == "AND":
            return all(condition_results)
        elif rule.logical_operator == "OR":
            return any(condition_results)
        else:
            return False
    
    def _evaluate_condition(self, condition: RuleCondition, condition_state: ConditionState, current_time: float) -> bool:
        """Evaluate a single condition against sensor data."""
        sensor_name = condition.sensor
        
        if sensor_name not in self._sensor_data:
            return False
        
        sensor_readings = self._sensor_data[sensor_name]
        if not sensor_readings:
            return False
        
        latest_reading = sensor_readings[-1]
        
        # Basic threshold operators
        if condition.operator == ">":
            return latest_reading.value > condition.threshold
        elif condition.operator == "<":
            return latest_reading.value < condition.threshold
        elif condition.operator == ">=":
            return latest_reading.value >= condition.threshold
        elif condition.operator == "<=":
            return latest_reading.value <= condition.threshold
        elif condition.operator == "==":
            return abs(latest_reading.value - condition.threshold) < 0.001
        elif condition.operator == "!=":
            return abs(latest_reading.value - condition.threshold) >= 0.001
        
        # Trend-based operators
        elif condition.operator in ["rate_increase", "rate_decrease"]:
            return self._evaluate_rate_condition(condition, sensor_readings, current_time)
        
        # Statistical operators
        elif condition.operator == "std_dev":
            return self._evaluate_statistical_condition(condition, sensor_readings, current_time)
        
        else:
            logger.warning(
                "Unknown condition operator",
                extra={"operator": condition.operator, "sensor": sensor_name}
            )
            return False
    
    def _evaluate_rate_condition(self, condition: RuleCondition, readings: deque, current_time: float) -> bool:
        """Evaluate rate of change conditions."""
        window_seconds = condition.window_seconds or 60.0
        cutoff_time = current_time - window_seconds
        
        # Get readings within the time window
        recent_readings = [r for r in readings if r.timestamp >= cutoff_time]
        
        if len(recent_readings) < 2:
            return False
        
        # Calculate rate of change
        oldest_value = recent_readings[0].value
        latest_value = recent_readings[-1].value
        change = latest_value - oldest_value
        
        if condition.operator == "rate_increase":
            return change >= condition.threshold
        elif condition.operator == "rate_decrease":
            return change <= -condition.threshold
        
        return False
    
    def _evaluate_statistical_condition(self, condition: RuleCondition, readings: deque, current_time: float) -> bool:
        """Evaluate statistical conditions like standard deviation."""
        window_seconds = condition.window_seconds or 300.0  # Default 5 minutes
        cutoff_time = current_time - window_seconds
        
        # Get readings within the time window
        recent_readings = [r for r in readings if r.timestamp >= cutoff_time]
        
        if len(recent_readings) < 3:  # Need at least 3 points for meaningful stats
            return False
        
        values = [r.value for r in recent_readings]
        
        if condition.operator == "std_dev":
            std_dev = statistics.stdev(values)
            return std_dev > condition.threshold
        
        return False
    
    async def _handle_rule_trigger(self, rule_state: RuleState, current_time: float) -> None:
        """Handle a rule that has just triggered."""
        rule = rule_state.rule
        
        # Check cooldown
        last_alert_time = self._alert_cooldowns.get(rule.id, 0.0)
        cooldown_seconds = rule.cooldown_seconds or self._config.alert_config.alert_cooldown_seconds
        
        if current_time - last_alert_time < cooldown_seconds:
            return  # Still in cooldown
        
        # Update state
        rule_state.is_currently_triggered = True
        rule_state.last_trigger_time = current_time
        rule_state.trigger_count += 1
        self._alert_cooldowns[rule.id] = current_time

        risk_score: int | None = None
        risk_level: str | None = None
        
        # Create violation record
        # Feed rule trigger into risk engine for R10 counting
        if self._risk_engine:
            self._risk_engine.process_safety_event(f"rule_violation:{rule.id}")
            risk_snapshot = self._risk_engine.evaluate(current_time)
            risk_score = risk_snapshot.risk_score
            risk_level = risk_snapshot.risk_level

        # Violations should always carry meaningful risk. If windowed risk rules
        # have not matured yet (score=0), assign a deterministic fallback weight.
        if not risk_score or risk_score <= 0:
            risk_score = self._fallback_risk_score(rule)
            risk_level = self._risk_level_for_score(risk_score)

        violation = self._create_violation_record(
            rule_state,
            current_time,
            risk_score=risk_score,
            risk_level=risk_level,
        )
        self._violation_history.append(violation)
        
        # Execute actions
        for action in rule.actions:
            try:
                await self._execute_action(action, rule, violation)
            except Exception as e:
                logger.error(
                    "Error executing rule action",
                    extra={
                        "event": "rule_action_error",
                        "rule_id": rule.id,
                        "action_type": action.type,
                        "error": str(e)
                    }
                )
        
        # Record violation event in scenario engine for demo UI
        await self._record_violation_event(rule, violation)
        
        logger.info(
            "Rule triggered",
            extra={
                "event": "rule_triggered",
                "rule_id": rule.id,
                "rule_name": rule.name,
                "severity": rule.severity,
                "persona_id": self._current_persona_id,
                "trigger_count": rule_state.trigger_count,
                "risk_score": risk_score,
                "risk_level": risk_level,
            }
        )

    def _fallback_risk_score(self, rule: Rule) -> int:
        """Return a non-zero fallback score for a triggered threshold rule."""
        score = _RULE_RISK_FALLBACK_WEIGHTS.get(rule.id)
        if score is not None:
            return score

        # Severity-based fallback for any unmapped custom rule IDs.
        severity_scores = {
            "info": 10,
            "warning": 30,
            "critical": 60,
            "emergency": 80,
        }
        return severity_scores.get(rule.severity, 10)

    def _risk_level_for_score(self, score: int) -> str:
        """Map a numeric score to risk level using configured thresholds."""
        if self._risk_engine:
            thresholds = self._risk_engine._config.level_thresholds
            if score >= thresholds.critical:
                return "critical"
            if score >= thresholds.high:
                return "high"
            if score >= thresholds.medium:
                return "medium"
            if score >= thresholds.low:
                return "low"
            return "none"

        # Default threshold mapping when risk engine is unavailable.
        if score >= 80:
            return "critical"
        if score >= 60:
            return "high"
        if score >= 30:
            return "medium"
        if score >= 10:
            return "low"
        return "none"
    
    def _create_violation_record(
        self,
        rule_state: RuleState,
        current_time: float,
        risk_score: int | None = None,
        risk_level: str | None = None,
    ) -> RuleViolation:
        """Create a violation record for the triggered rule."""
        rule = rule_state.rule
        
        # Collect current sensor readings
        sensor_readings = {}
        triggered_conditions = []
        
        for condition in rule.conditions:
            sensor_name = condition.sensor
            if sensor_name in self._sensor_data and self._sensor_data[sensor_name]:
                latest_reading = self._sensor_data[sensor_name][-1]
                sensor_readings[sensor_name] = {
                    "value": latest_reading.value,
                    "condition": latest_reading.condition,
                    "quality": latest_reading.quality,
                }
                
                # Check if this condition is currently violated
                condition_state = rule_state.condition_states[sensor_name]
                if condition_state.is_currently_violated:
                    triggered_conditions.append(f"{sensor_name} {condition.operator} {condition.threshold}")
        
        return RuleViolation(
            rule_id=rule.id,
            rule_name=rule.name,
            timestamp=current_time,
            persona_id=self._current_persona_id or "unknown",
            poc_type=self._current_poc_type or "unknown",
            sensor_readings=sensor_readings,
            triggered_conditions=triggered_conditions,
            severity=rule.severity,
            category=rule.category,
            risk_score=risk_score,
            risk_level=risk_level,
        )
    
    async def _execute_action(self, action: RuleAction, rule: Rule, violation: RuleViolation) -> None:
        """Execute a single rule action."""
        
        if action.type == "log":
            await self._execute_log_action(action, rule, violation)
        elif action.type == "mqtt_alert":
            await self._execute_mqtt_alert_action(action, rule, violation)
        elif action.type == "scenario_event":
            await self._execute_scenario_event_action(action, rule, violation)
        elif action.type == "webhook":
            await self._execute_webhook_action(action, rule, violation)
        else:
            logger.warning(
                "Unknown action type",
                extra={"action_type": action.type, "rule_id": rule.id}
            )
    
    async def _execute_log_action(self, action: RuleAction, rule: Rule, violation: RuleViolation) -> None:
        """Execute a log action."""
        message = action.message or f"Rule '{rule.name}' triggered"
        
        # Substitute placeholders
        message = self._substitute_placeholders(message, rule, violation)
        
        # Apply severity template
        template = self._config.alert_config.templates.get(rule.severity, "{message}")
        formatted_message = template.format(
            rule_name=rule.name,
            message=message
        )
        
        # Log at appropriate level
        if rule.severity == "critical" or rule.severity == "emergency":
            logger.error(formatted_message, extra={"event": "rule_alert", "rule_id": rule.id})
        elif rule.severity == "warning":
            logger.warning(formatted_message, extra={"event": "rule_alert", "rule_id": rule.id})
        else:
            logger.info(formatted_message, extra={"event": "rule_alert", "rule_id": rule.id})
    
    async def _execute_mqtt_alert_action(self, action: RuleAction, rule: Rule, violation: RuleViolation) -> None:
        """Execute an MQTT alert action."""
        if not self._mqtt_publisher:
            logger.warning("No MQTT publisher available for alert", extra={"rule_id": rule.id})
            return
        
        # Build topic
        topic = action.topic or f"{self._config.alert_config.mqtt_topic_prefix}/{rule.category}/{violation.persona_id}/{rule.id}"
        topic = self._substitute_placeholders(topic, rule, violation)
        
        # Build payload
        if action.payload:
            # Use custom payload
            payload = action.payload.copy()
            # Substitute placeholders in payload values
            for key, value in payload.items():
                if isinstance(value, str):
                    payload[key] = self._substitute_placeholders(value, rule, violation)
        else:
            # Default payload
            payload = {
                "rule_id": rule.id,
                "rule_name": rule.name,
                "severity": rule.severity,
                "category": rule.category,
                "timestamp": datetime.fromtimestamp(violation.timestamp, tz=timezone.utc).isoformat(),
                "persona_id": violation.persona_id,
                "poc_type": violation.poc_type,
                "triggered_conditions": violation.triggered_conditions,
                "risk_score": violation.risk_score,
                "risk_level": violation.risk_level,
            }
            
            if self._config.alert_config.include_sensor_context:
                payload["sensor_readings"] = violation.sensor_readings
        
        # Publish alert
        try:
            payload_json = json.dumps(payload)
            self._mqtt_publisher.publish_raw(topic, payload_json, qos=1)
            
            logger.debug(
                "MQTT alert published",
                extra={
                    "event": "mqtt_alert_published",
                    "rule_id": rule.id,
                    "topic": topic
                }
            )
        except Exception as e:
            logger.error(
                "Failed to publish MQTT alert",
                extra={
                    "event": "mqtt_alert_error",
                    "rule_id": rule.id,
                    "error": str(e)
                }
            )
    
    async def _execute_scenario_event_action(self, action: RuleAction, rule: Rule, violation: RuleViolation) -> None:
        """Execute a scenario event action."""
        if not self._scenario_engine:
            logger.warning("No scenario engine available for event trigger", extra={"rule_id": rule.id})
            return
        
        event_name = action.event_name
        if not event_name:
            logger.warning("No event_name specified for scenario_event action", extra={"rule_id": rule.id})
            return
        
        # Trigger the event in the scenario engine
        try:
            await self._scenario_engine.trigger_manual_event(event_name)
            
            logger.info(
                "Scenario event triggered by rule",
                extra={
                    "event": "scenario_event_triggered",
                    "rule_id": rule.id,
                    "event_name": event_name
                }
            )
        except Exception as e:
            logger.error(
                "Failed to trigger scenario event",
                extra={
                    "event": "scenario_event_error",
                    "rule_id": rule.id,
                    "event_name": event_name,
                    "error": str(e)
                }
            )
    
    async def _execute_webhook_action(self, action: RuleAction, rule: Rule, violation: RuleViolation) -> None:
        """Execute a webhook action."""
        # This would require an HTTP client - placeholder for now
        logger.info(
            "Webhook action triggered (not implemented)",
            extra={
                "event": "webhook_triggered",
                "rule_id": rule.id,
                "url": action.url
            }
        )
    
    def _substitute_placeholders(self, text: str, rule: Rule, violation: RuleViolation) -> str:
        """Substitute placeholders in action strings."""
        placeholders = {
            "rule_id": rule.id,
            "rule_name": rule.name,
            "persona_id": violation.persona_id,
            "poc_type": violation.poc_type,
            "timestamp": datetime.fromtimestamp(violation.timestamp, tz=timezone.utc).isoformat(),
            "severity": rule.severity,
            "category": rule.category,
            "risk_score": violation.risk_score if violation.risk_score is not None else "n/a",
            "risk_level": violation.risk_level if violation.risk_level is not None else "none",
        }
        
        # Add sensor values
        for sensor_name, reading in violation.sensor_readings.items():
            placeholders[sensor_name] = reading["value"]
            placeholders[f"{sensor_name}_condition"] = reading["condition"]
            placeholders[f"{sensor_name}_quality"] = reading["quality"]
        
        # Simple placeholder substitution
        result = text
        for key, value in placeholders.items():
            result = result.replace(f"{{{key}}}", str(value))
        
        return result
    
    # ---------------------------------------------------------------------------
    # Status and management methods
    # ---------------------------------------------------------------------------
    
    def get_status(self) -> Dict[str, Any]:
        """Get current engine status and statistics."""
        total_rules = len(self._config.rules)
        enabled_rules = len([r for r in self._config.rules if r.enabled])
        active_rules = len([rs for rs in self._rule_states.values() if rs.is_currently_triggered])

        risk_status = self._risk_engine.get_status() if self._risk_engine else {
            "enabled": False,
            "risk_score": 0,
            "risk_level": "none",
            "active_rule_ids": [],
        }
        
        return {
            "running": self._running,
            "total_rules": total_rules,
            "enabled_rules": enabled_rules,
            "active_rules": active_rules,
            "violation_count": len(self._violation_history),
            "last_evaluation_time": self._last_evaluation_time,
            "evaluation_interval": self._config.engine.evaluation_interval_seconds,
            "risk": risk_status,
        }
    
    def get_rule_states(self) -> Dict[str, Any]:
        """Get current state of all rules."""
        states = {}
        for rule_id, rule_state in self._rule_states.items():
            states[rule_id] = {
                "name": rule_state.rule.name,
                "enabled": rule_state.rule.enabled,
                "triggered": rule_state.is_currently_triggered,
                "trigger_count": rule_state.trigger_count,
                "last_trigger_time": rule_state.last_trigger_time,
                "conditions": {
                    sensor: {
                        "violated": cs.is_currently_violated,
                        "violation_count": cs.violation_count,
                        "first_violation_time": cs.first_violation_time,
                    }
                    for sensor, cs in rule_state.condition_states.items()
                }
            }
        return states
    
    def get_recent_violations(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent rule violations."""
        recent = list(self._violation_history)[-limit:]
        return [
            {
                "rule_id": v.rule_id,
                "rule_name": v.rule_name,
                "timestamp": v.timestamp,
                "persona_id": v.persona_id,
                "severity": v.severity,
                "category": v.category,
                "triggered_conditions": v.triggered_conditions,
                "sensor_readings": v.sensor_readings,
                "risk_score": v.risk_score,
                "risk_level": v.risk_level,
            }
            for v in recent
        ]
    
    def enable_rule(self, rule_id: str) -> bool:
        """Enable a rule by ID. Returns True if successful."""
        for rule in self._config.rules:
            if rule.id == rule_id:
                rule.enabled = True
                if rule_id not in self._rule_states:
                    # Initialize state for newly enabled rule
                    condition_states = {}
                    for condition in rule.conditions:
                        condition_states[condition.sensor] = ConditionState(condition=condition)
                    
                    self._rule_states[rule.id] = RuleState(
                        rule=rule,
                        condition_states=condition_states,
                    )
                
                logger.info(f"Rule enabled: {rule_id}")
                return True
        return False
    
    def disable_rule(self, rule_id: str) -> bool:
        """Disable a rule by ID. Returns True if successful."""
        for rule in self._config.rules:
            if rule.id == rule_id:
                rule.enabled = False
                if rule_id in self._rule_states:
                    # Reset rule state
                    rule_state = self._rule_states[rule_id]
                    rule_state.is_currently_triggered = False
                    for condition_state in rule_state.condition_states.values():
                        condition_state.is_currently_violated = False
                        condition_state.first_violation_time = None
                        condition_state.violation_count = 0
                
                logger.info(f"Rule disabled: {rule_id}")
                return True
        return False

    def update_risk_context(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Update risk scoring context (zone/machine/PPE/shift/location)."""
        if not self._risk_engine:
            return {"enabled": False, "message": "Risk scoring engine not configured"}

        self._risk_engine.process_context_update(context)
        snapshot = self._risk_engine.evaluate(time.time())
        return {
            "enabled": True,
            "risk_score": snapshot.risk_score,
            "risk_level": snapshot.risk_level,
            "active_rule_ids": snapshot.active_rule_ids,
        }

    def get_risk_status(self) -> Dict[str, Any]:
        """Get risk scoring status for R1-R10 engine."""
        if not self._risk_engine:
            return {"enabled": False, "message": "Risk scoring engine not configured"}
        return self._risk_engine.get_status()

    def get_configured_risk_rules(self) -> List[Dict[str, Any]]:
        """Get all configured risk rules with their metadata."""
        if not self._risk_engine:
            return []
        return self._risk_engine.get_configured_risk_rules()

    def get_recent_risk_snapshots(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent risk snapshots for dashboard/troubleshooting."""
        if not self._risk_engine:
            return []
        return self._risk_engine.get_recent_snapshots(limit=limit)
    
    async def _record_violation_event(self, rule: Rule, violation: RuleViolation) -> None:
        """Record violation event in scenario engine for demo UI display."""
        if not self._scenario_engine:
            return
        
        # Create a descriptive event name for the UI
        event_name = f"rule_violation_{rule.id}"
        
        # Add to the scenario engine's events_fired list
        # This will make it appear in the demo UI event log
        if hasattr(self._scenario_engine, '_events_fired'):
            self._scenario_engine._events_fired.append(event_name)
    
    async def _record_violation_event(self, rule: Rule, violation: RuleViolation) -> None:
        """Record violation event in scenario engine for demo UI display."""
        if not self._scenario_engine:
            return
        
        # Create a descriptive event name for the UI
        event_name = f"rule_violation_{rule.id}"
        
        # Add to the scenario engine's events_fired list
        # This will make it appear in the demo UI event log
        if hasattr(self._scenario_engine, '_events_fired'):
            self._scenario_engine._events_fired.append(event_name)
            
            # Also log the event details for the demo UI event log
            if hasattr(self._scenario_engine, '_demo_event_log'):
                # If there's a demo event log, add detailed info
                event_details = {
                    "type": "violation",
                    "rule_name": rule.name,
                    "severity": rule.severity,
                    "conditions": violation.triggered_conditions,
                    "timestamp": violation.timestamp,
                    "risk_score": violation.risk_score,
                    "risk_level": violation.risk_level,
                }
                self._scenario_engine._demo_event_log.append(event_details)
