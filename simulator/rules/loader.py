"""
simulator/rules/loader.py
---------------------------
Rules configuration loader for the Vitals Simulator.

Loads and validates rules from YAML configuration files.
Provides type-safe dataclasses for all rule components.

Usage:
    from simulator.rules import load_rules_config
    rules_config = load_rules_config("config/rules_config.yaml")
    for rule in rules_config.rules:
        print(f"Rule: {rule.name}")
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from simulator.utils.logger import get_logger

logger = get_logger(__name__)


class RulesConfigError(Exception):
    """Raised when rules configuration is invalid."""
    pass


# ---------------------------------------------------------------------------
# Rule condition operators
# ---------------------------------------------------------------------------

@dataclass
class RuleCondition:
    """A single condition within a rule."""
    sensor: str
    operator: str  # >, <, >=, <=, ==, !=, rate_increase, rate_decrease, std_dev
    threshold: float
    duration_seconds: float = 0.0
    window_seconds: Optional[float] = None  # For trend/statistical operators


@dataclass
class RuleAction:
    """An action to execute when a rule triggers."""
    type: str  # log, mqtt_alert, scenario_event, webhook
    message: Optional[str] = None
    topic: Optional[str] = None
    event_name: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
    url: Optional[str] = None


@dataclass
class TimeConstraint:
    """Time-based constraints for rule activation."""
    start_hour: Optional[int] = None
    end_hour: Optional[int] = None
    days_of_week: Optional[List[int]] = None  # 0=Monday, 6=Sunday


@dataclass
class Rule:
    """A complete monitoring rule definition."""
    id: str
    name: str
    description: str
    category: str
    severity: str  # info, warning, critical, emergency
    enabled: bool
    conditions: List[RuleCondition]
    actions: List[RuleAction]
    logical_operator: str = "AND"  # AND, OR
    time_constraints: Optional[TimeConstraint] = None
    cooldown_seconds: Optional[float] = None


@dataclass
class RulesEngineConfig:
    """Configuration for the rules engine."""
    evaluation_interval_seconds: float = 1.0
    max_violation_history: int = 1000
    log_evaluations: bool = False
    publish_violations: bool = True


@dataclass
class AlertConfig:
    """Configuration for alert handling."""
    mqtt_topic_prefix: str = "alerts"
    include_sensor_context: bool = True
    alert_cooldown_seconds: float = 30.0
    templates: Dict[str, str] = field(default_factory=dict)


@dataclass
class RulesConfig:
    """Complete rules configuration."""
    schema_version: str
    engine: RulesEngineConfig
    rules: List[Rule]
    alert_config: AlertConfig


# ---------------------------------------------------------------------------
# Loader functions
# ---------------------------------------------------------------------------

def load_rules_config(path: Path | str) -> RulesConfig:
    """
    Load and validate a rules configuration file.
    
    Args:
        path: Path to the rules YAML file.
        
    Returns:
        Validated RulesConfig object.
        
    Raises:
        RulesConfigError: If the configuration is invalid.
    """
    path = Path(path)
    
    if not path.exists():
        raise RulesConfigError(f"Rules config file not found: {path}")
    
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise RulesConfigError(f"Invalid YAML in {path}: {e}")
    
    if not data:
        raise RulesConfigError(f"Empty rules config file: {path}")
    
    try:
        return _parse_rules_config(data)
    except Exception as e:
        raise RulesConfigError(f"Invalid rules configuration: {e}")


def _parse_rules_config(data: Dict[str, Any]) -> RulesConfig:
    """Parse and validate the rules configuration dictionary."""
    
    # Validate schema version
    schema_version = data.get('schema_version', '1.0')
    if schema_version != '1.0':
        raise RulesConfigError(f"Unsupported schema version: {schema_version}")
    
    # Parse engine config
    engine_data = data.get('engine', {})
    engine = RulesEngineConfig(
        evaluation_interval_seconds=float(engine_data.get('evaluation_interval_seconds', 1.0)),
        max_violation_history=int(engine_data.get('max_violation_history', 1000)),
        log_evaluations=bool(engine_data.get('log_evaluations', False)),
        publish_violations=bool(engine_data.get('publish_violations', True)),
    )
    
    # Parse alert config
    alert_data = data.get('alert_config', {})
    alert_config = AlertConfig(
        mqtt_topic_prefix=alert_data.get('mqtt_topic_prefix', 'alerts'),
        include_sensor_context=bool(alert_data.get('include_sensor_context', True)),
        alert_cooldown_seconds=float(alert_data.get('alert_cooldown_seconds', 30.0)),
        templates=alert_data.get('templates', {}),
    )
    
    # Parse rules
    rules_data = data.get('rules', [])
    rules = []
    
    for rule_data in rules_data:
        rule = _parse_rule(rule_data)
        rules.append(rule)
    
    logger.info(
        "Rules configuration loaded",
        extra={
            "event": "rules_config_loaded",
            "rules_count": len(rules),
            "enabled_rules": sum(1 for r in rules if r.enabled),
            "evaluation_interval": engine.evaluation_interval_seconds,
        }
    )
    
    return RulesConfig(
        schema_version=schema_version,
        engine=engine,
        rules=rules,
        alert_config=alert_config,
    )


def _parse_rule(data: Dict[str, Any]) -> Rule:
    """Parse a single rule from configuration data."""
    
    # Required fields
    rule_id = data.get('id')
    if not rule_id:
        raise RulesConfigError("Rule missing required 'id' field")
    
    name = data.get('name', rule_id)
    description = data.get('description', '')
    category = data.get('category', 'general')
    severity = data.get('severity', 'info')
    enabled = bool(data.get('enabled', True))
    
    # Validate severity
    valid_severities = {'info', 'warning', 'critical', 'emergency'}
    if severity not in valid_severities:
        raise RulesConfigError(
            f"Invalid severity '{severity}' for rule '{rule_id}'. "
            f"Must be one of: {', '.join(valid_severities)}"
        )
    
    # Parse conditions
    conditions_data = data.get('conditions', [])
    if not conditions_data:
        raise RulesConfigError(f"Rule '{rule_id}' has no conditions")
    
    conditions = []
    for condition_data in conditions_data:
        condition = _parse_condition(condition_data, rule_id)
        conditions.append(condition)
    
    # Parse actions
    actions_data = data.get('actions', [])
    actions = []
    for action_data in actions_data:
        action = _parse_action(action_data, rule_id)
        actions.append(action)
    
    # Parse optional fields
    logical_operator = data.get('logical_operator', 'AND').upper()
    if logical_operator not in {'AND', 'OR'}:
        raise RulesConfigError(
            f"Invalid logical_operator '{logical_operator}' for rule '{rule_id}'. "
            "Must be 'AND' or 'OR'"
        )
    
    # Parse time constraints
    time_constraints = None
    if 'time_constraints' in data:
        tc_data = data['time_constraints']
        time_constraints = TimeConstraint(
            start_hour=tc_data.get('start_hour'),
            end_hour=tc_data.get('end_hour'),
            days_of_week=tc_data.get('days_of_week'),
        )
    
    cooldown_seconds = data.get('cooldown_seconds')
    if cooldown_seconds is not None:
        cooldown_seconds = float(cooldown_seconds)
    
    return Rule(
        id=rule_id,
        name=name,
        description=description,
        category=category,
        severity=severity,
        enabled=enabled,
        conditions=conditions,
        actions=actions,
        logical_operator=logical_operator,
        time_constraints=time_constraints,
        cooldown_seconds=cooldown_seconds,
    )


def _parse_condition(data: Dict[str, Any], rule_id: str) -> RuleCondition:
    """Parse a rule condition from configuration data."""
    
    sensor = data.get('sensor')
    if not sensor:
        raise RulesConfigError(f"Condition in rule '{rule_id}' missing 'sensor' field")
    
    operator = data.get('operator')
    if not operator:
        raise RulesConfigError(f"Condition in rule '{rule_id}' missing 'operator' field")
    
    # Validate operator
    valid_operators = {
        '>', '<', '>=', '<=', '==', '!=',
        'rate_increase', 'rate_decrease', 'std_dev'
    }
    if operator not in valid_operators:
        raise RulesConfigError(
            f"Invalid operator '{operator}' in rule '{rule_id}'. "
            f"Must be one of: {', '.join(valid_operators)}"
        )
    
    threshold = data.get('threshold')
    if threshold is None:
        raise RulesConfigError(f"Condition in rule '{rule_id}' missing 'threshold' field")
    
    try:
        threshold = float(threshold)
    except (ValueError, TypeError):
        raise RulesConfigError(
            f"Invalid threshold '{threshold}' in rule '{rule_id}' - must be a number"
        )
    
    duration_seconds = float(data.get('duration_seconds', 0.0))
    window_seconds = data.get('window_seconds')
    if window_seconds is not None:
        window_seconds = float(window_seconds)
    
    return RuleCondition(
        sensor=sensor,
        operator=operator,
        threshold=threshold,
        duration_seconds=duration_seconds,
        window_seconds=window_seconds,
    )


def _parse_action(data: Dict[str, Any], rule_id: str) -> RuleAction:
    """Parse a rule action from configuration data."""
    
    action_type = data.get('type')
    if not action_type:
        raise RulesConfigError(f"Action in rule '{rule_id}' missing 'type' field")
    
    # Validate action type
    valid_types = {'log', 'mqtt_alert', 'scenario_event', 'webhook'}
    if action_type not in valid_types:
        raise RulesConfigError(
            f"Invalid action type '{action_type}' in rule '{rule_id}'. "
            f"Must be one of: {', '.join(valid_types)}"
        )
    
    return RuleAction(
        type=action_type,
        message=data.get('message'),
        topic=data.get('topic'),
        event_name=data.get('event_name'),
        payload=data.get('payload'),
        url=data.get('url'),
    )


def get_default_rules_config() -> RulesConfig:
    """
    Get a default rules configuration if no config file exists.
    
    Returns:
        Basic RulesConfig with sensible defaults.
    """
    return RulesConfig(
        schema_version="1.0",
        engine=RulesEngineConfig(),
        rules=[],
        alert_config=AlertConfig(),
    )
