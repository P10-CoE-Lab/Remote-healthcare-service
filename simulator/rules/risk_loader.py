"""
simulator/rules/risk_loader.py
------------------------------
Risk scoring configuration loader for worker safety risk rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import yaml


class RiskConfigError(Exception):
    """Raised when risk scoring configuration is invalid."""


@dataclass
class RiskRule:
    """One risk rule with condition metadata and score weight."""
    id: str
    description: str
    weight: int
    evaluation_window_seconds: float
    condition_type: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True


@dataclass
class RiskLevelThresholds:
    """Risk score to level mapping."""
    low: int = 10
    medium: int = 30
    high: int = 60
    critical: int = 80


@dataclass
class RiskScoringConfig:
    """Complete risk scoring configuration."""
    schema_version: str
    level_thresholds: RiskLevelThresholds
    rules: List[RiskRule]


def load_risk_scoring_config(path: Path | str) -> RiskScoringConfig:
    """Load and validate risk scoring config from YAML."""
    cfg_path = Path(path)
    if not cfg_path.exists():
        return get_default_risk_scoring_config()

    try:
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise RiskConfigError(f"Invalid risk scoring YAML: {exc}") from exc

    return _parse_risk_config(data)


def _parse_risk_config(data: Dict[str, Any]) -> RiskScoringConfig:
    schema_version = str(data.get("schema_version", "1.0"))
    thresholds_data = data.get("risk_levels", {})
    thresholds = RiskLevelThresholds(
        low=int(thresholds_data.get("low", 10)),
        medium=int(thresholds_data.get("medium", 30)),
        high=int(thresholds_data.get("high", 60)),
        critical=int(thresholds_data.get("critical", 80)),
    )

    rules_raw = data.get("rules", [])
    rules: List[RiskRule] = []
    for item in rules_raw:
        rule_id = str(item.get("id", "")).strip()
        if not rule_id:
            raise RiskConfigError("Risk rule missing id")

        condition = item.get("condition", {})
        condition_type = str(condition.get("type", "")).strip()
        if not condition_type:
            raise RiskConfigError(f"Risk rule '{rule_id}' missing condition.type")

        rules.append(
            RiskRule(
                id=rule_id,
                description=str(item.get("description", "")),
                weight=int(item.get("weight", 0)),
                evaluation_window_seconds=float(item.get("evaluation_window_seconds", 0)),
                condition_type=condition_type,
                parameters=dict(condition.get("parameters", {})),
                enabled=bool(item.get("enabled", True)),
            )
        )

    if not rules:
        return get_default_risk_scoring_config()

    return RiskScoringConfig(
        schema_version=schema_version,
        level_thresholds=thresholds,
        rules=rules,
    )


def get_default_risk_scoring_config() -> RiskScoringConfig:
    """Return default config implementing R1-R10 rules."""
    return RiskScoringConfig(
        schema_version="1.0",
        level_thresholds=RiskLevelThresholds(low=10, medium=30, high=60, critical=80),
        rules=[
            RiskRule("R1", "Worker enters restricted/hazard zone", 40, 0, "hazard_zone_entry", {"zone_types": ["hazard", "restricted"]}),
            RiskRule("R2", "Worker too close to running machine", 60, 0, "unsafe_machine_proximity", {"danger_radius": 2.0}),
            RiskRule("R3", "Worker stationary near running machine", 50, 10, "stationary_near_running_machine", {"motion_threshold": 0.08, "danger_radius": 2.0}),
            RiskRule("R4", "PPE violation in hazardous zone", 35, 0, "ppe_violation_hazard_zone", {}),
            RiskRule("R5", "Fall detected by wearable", 80, 0, "fall_detected", {"accel_fall_threshold": 3.0}),
            RiskRule("R6", "Abnormal heart rate spike", 30, 60, "heart_rate_spike", {"heart_rate_threshold": 140}),
            RiskRule("R7", "Fatigue risk during late shift", 25, 120, "late_shift_fatigue", {"fatigue_hr_threshold": 95, "hours_worked_threshold": 6}),
            RiskRule("R8", "Worker remains in hazard zone too long", 20, 300, "hazard_zone_dwell_time", {"time_in_zone_threshold_seconds": 120}),
            RiskRule("R9", "Machine overheating risk near worker", 40, 30, "machine_overheating_near_worker", {"machine_temperature_threshold": 85, "danger_radius": 2.0}),
            RiskRule("R10", "Multiple violations in short period", 50, 180, "multiple_violations_short_period", {"events_threshold": 3}),
        ],
    )
