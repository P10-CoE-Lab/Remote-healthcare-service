"""rule_engine/edge/config.py — load and validate edge_rules.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class EdgeRule:
    id:                      str
    description:             str
    sensor:                  str
    operator:                str     # "<" | ">" | "<=" | ">=" | "=="
    threshold:               float
    severity:                str
    cooldown_seconds:        float
    min_observation_seconds: float   # must breach continuously for this long before firing
    consecutive_readings:    int     # last N readings must all breach (noise rejection)


@dataclass
class EdgeConfig:
    alert_topic_prefix:      str
    default_cooldown_seconds: float
    rules:                   list[EdgeRule] = field(default_factory=list)


def load_edge_config(path: Path | str) -> EdgeConfig:
    """Load edge_rules.yaml and return a validated EdgeConfig."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Edge rules config not found: {path}")

    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    default_cooldown     = float(raw.get("default_cooldown_seconds", 30))
    default_observation  = float(raw.get("default_min_observation_seconds", 5))
    default_consecutive  = int(raw.get("default_consecutive_readings", 3))
    topic_prefix         = raw.get("alert_topic_prefix", "alerts/edge")

    rules: list[EdgeRule] = []
    for r in raw.get("rules", []):
        rules.append(EdgeRule(
            id=                      str(r["id"]),
            description=             str(r.get("description", "")),
            sensor=                  str(r["sensor"]),
            operator=                str(r["operator"]),
            threshold=               float(r["threshold"]),
            severity=                str(r.get("severity", "warning")),
            cooldown_seconds=        float(r.get("cooldown_seconds", default_cooldown)),
            min_observation_seconds= float(r.get("min_observation_seconds", default_observation)),
            consecutive_readings=    int(r.get("consecutive_readings", default_consecutive)),
        ))

    return EdgeConfig(
        alert_topic_prefix=       topic_prefix,
        default_cooldown_seconds= default_cooldown,
        rules=                    rules,
    )
