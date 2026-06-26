"""rule_engine/cloud/config.py — load and validate cloud_rules.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class CloudCondition:
    sensor:    str
    operator:  str
    threshold: float


@dataclass
class CloudRule:
    id:                  str
    description:         str
    window_seconds:      float
    fraction_required:   float
    weight:              float
    severity:            str
    cooldown_seconds:    float
    # Single condition (most rules)
    condition:           CloudCondition | None = None
    # Multi-condition (AND — all must be sustained simultaneously)
    multi_conditions:    list[CloudCondition] = field(default_factory=list)


@dataclass
class CloudConfig:
    alert_topic_prefix:          str
    default_cooldown_seconds:    float
    evaluation_interval_seconds: float
    default_fraction_required:   float
    risk_levels:                 dict[str, float]
    rules:                       list[CloudRule] = field(default_factory=list)


def load_cloud_config(path: Path | str) -> CloudConfig:
    """Load cloud_rules.yaml and return a validated CloudConfig."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Cloud rules config not found: {path}")

    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    default_cooldown   = float(raw.get("default_cooldown_seconds", 60))
    eval_interval      = float(raw.get("evaluation_interval_seconds", 5))
    default_fraction   = float(raw.get("default_fraction_required", 0.70))
    topic_prefix       = raw.get("alert_topic_prefix", "alerts/cloud")
    risk_levels        = {k: float(v) for k, v in raw.get("risk_levels", {}).items()}

    rules: list[CloudRule] = []
    for r in raw.get("rules", []):
        # Single condition
        cond = None
        if "condition" in r:
            c = r["condition"]
            cond = CloudCondition(
                sensor=    str(c["sensor"]),
                operator=  str(c["operator"]),
                threshold= float(c["threshold"]),
            )

        # Multi conditions (AND)
        multi = []
        for mc in r.get("multi_conditions", []):
            multi.append(CloudCondition(
                sensor=    str(mc["sensor"]),
                operator=  str(mc["operator"]),
                threshold= float(mc["threshold"]),
            ))

        rules.append(CloudRule(
            id=                str(r["id"]),
            description=       str(r.get("description", "")),
            window_seconds=    float(r.get("window_seconds", 30)),
            fraction_required= float(r.get("fraction_required", default_fraction)),
            weight=            float(r.get("weight", 0)),
            severity=          str(r.get("severity", "warning")),
            cooldown_seconds=  float(r.get("cooldown_seconds", default_cooldown)),
            condition=         cond,
            multi_conditions=  multi,
        ))

    return CloudConfig(
        alert_topic_prefix=          topic_prefix,
        default_cooldown_seconds=    default_cooldown,
        evaluation_interval_seconds= eval_interval,
        default_fraction_required=   default_fraction,
        risk_levels=                 risk_levels,
        rules=                       rules,
    )
