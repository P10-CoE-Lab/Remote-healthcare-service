"""
simulator/rules/timing_policy.py
--------------------------------
Unified timing policy loader for both threshold rules and risk rules.

This provides a single policy table that governs:
- duration_seconds for threshold rules (rules_config.yaml)
- evaluation_window_seconds for risk rules (risk_rules.yaml)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict

import yaml


@dataclass
class TimingPolicy:
    """Unified timing policy keyed by rule ID."""
    rule_windows_seconds: Dict[str, float]


def load_timing_policy(path: Path | str) -> TimingPolicy:
    """Load timing policy file, returning empty policy if missing/invalid."""
    cfg_path = Path(path)
    if not cfg_path.exists():
        return TimingPolicy(rule_windows_seconds={})

    try:
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return TimingPolicy(rule_windows_seconds={})

    windows_raw = raw.get("rule_windows_seconds", {})
    if not isinstance(windows_raw, dict):
        return TimingPolicy(rule_windows_seconds={})

    normalized: Dict[str, float] = {}
    for key, value in windows_raw.items():
        try:
            normalized[str(key)] = float(value)
        except (TypeError, ValueError):
            continue

    return TimingPolicy(rule_windows_seconds=normalized)
